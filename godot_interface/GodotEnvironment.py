import socket
import json
import numpy as np
import os
import subprocess
import ast
from .utils import get_path, get_godot_path, get_godot_package_path
from typing import Optional, List

import struct


def recv_msg(sock):
    # Read message length and unpack it into an integer
    raw_msglen = recvall(sock, 4)
    if not raw_msglen:
        return None
        
    msglen = struct.unpack('>I', raw_msglen)[0]
    # Read the message data
    return recvall(sock, msglen)

def recvall(sock, n):
    # Helper function to recv n bytes or return None if EOF is hit
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return data

class GodotEnvironment:
    def __init__(
        self, 
        host: Optional[str] = '127.0.0.1',
        port: Optional[int] = 4242,
        env_name : Optional[str] = "", 
        agent_names : Optional[List[str]] = [],
        state_min : Optional[List[int]] = [0, 0],
        state_max : Optional[List[int]] = [1000, 1000],
        display_actions : Optional[bool] = False,
        display_states : Optional[bool] = False,
        verbose : Optional[bool] = False,
        seed: Optional[int] = np.random.randint(0, 1e5),
        max_rec_bits: Optional[int] = 10000000
    ):
        self.host = host
        self.port = port
        self.socket = None
        self.client_socket = None

        self.godot_path_str = get_godot_path()
        self.env_path_str = get_godot_package_path(env_name)

        self.godot_process = None
        self.is_godot_launched = False
        self.is_rendering = True

        self.agent_names = agent_names
        self.state_min = state_min
        self.state_max = state_max

        self.display_actions = display_actions
        self.display_states = display_states
        self.verbose = verbose
        self.seed = seed
        self.random_generator = np.random.RandomState(seed=self.seed)

        self.max_rec_bits = max_rec_bits

        self.metrics = {
            "regions": [], 
            "misc": []
        }

    def set_seed(self, seed):
        self.seed = seed
        self.random_generator = np.random.RandomState(seed=seed)

    # main functions ===================================================

    def reset(self, render: bool):
        """
        Initialize the environment and returns its first state.
        To do so, it:
        - handles the rendering type
        - Creates a godot simulation instance in a subprocess if it is needed
        - Creates a tcp connexion with the simulation
        - Gets the initial state of the environment through the tcp connection
        - Scale the state
        :param render: boolean, indicates whether the simulator displays, - in which case the game executes 
        at normal speed - or not - the game executes at a higher rate (max 17 times faster, for now)
        :return: initial state of the environment (dictionary)
        """
        # change render type and end simulation to restart it with the right parameter later if specified so.
        self._change_render_type_if_needed(render)

        # Initializing a subprocess where a godot instance is launched, if it doesn't exist yet.
        self._launch_simulation_if_needed()
        print("the simulation  should be launched")

        # Initializing the socket if it's not already done.
        if self.socket is None:
            self._initialize_socket()
            # Creating the connexion with the simulator
            self._wait_for_connection()
        
        print("connection should be established")
        # Send the first request to get the initial state of the simulation
        first_request = self._create_request(initialization=True)
        self.client_socket.sendall(first_request)

        # Get the first state of the simulation, scale it and return it
        env_data = self._get_environment_state()
        if self.display_states:
            print(env_data)
        states_data = env_data["states_data"]

        self.metrics = {}
        self.metrics["regions"] = []
        self.metrics["misc"] = []
        #states_data = self.scale_states_data(states_data)

        return states_data

    def step(self, actions_data):
        """
        sending an action to the godot agent and returns the reward it 
        earned, the new state of the environment and a boolean indicating 
        whether the game is done.
        :param action_data: dictionary
        :return: states_data (dic), rewards_data (dic), done (boolean), n_frames (int)
        """
        # prepare and send data to simulation
        request = self._create_request(actions_data=actions_data)
        if self.display_actions:
            print(request)
        self.client_socket.sendall(request)

        # receive environment data
        env_data = self._get_environment_state()
        if self.display_states:
            print(env_data)
        
        # splitting data
        states_data, rewards_data = self._split_env_data(env_data["states_data"])
        #print(rewards_data)

        # Test to plot a metric
        # TODO: refactor that
        metrics_data = env_data["states_data"][0]["metrics"]
        self.metrics["regions"].append(metrics_data["region"])
        if metrics_data["misc"]:
            self.metrics["misc"].append(metrics_data["misc"])


        n_frames = env_data["n_frames"]
        # scaling reward
        for n_agent in range(len(rewards_data)):
            rewards_data[n_agent]["reward"] /= n_frames
        # scaling states
        # states_data = self.scale_states_data(states_data)

        # handling ending condition
        done = env_data["done"]
        if done:
            pass
            # self._end_connection() # I used to do it.
            #self.close()


        return states_data, rewards_data, done, n_frames

    def close(self):
        """Properly closes the environment and the connection"""
        # print(self.godot_process.poll())

        termination_request = self._create_request(termination=True)
        self.client_socket.sendall(termination_request)
        self._end_connection()
        # self.godot_process.kill()

        self.godot_process.wait()
        self.is_godot_launched = False

    # Connection functions =============================================
    # ==== sockets         =============================================

    def _initialize_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def _wait_for_connection(self):
        """
        Runs until a connection is made
        :return:
        """
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen()
        self.client_socket, addr = self.socket.accept()
        if self.verbose:
            print('Connected by', addr)

    def _end_connection(self):
        """Closes the socket, and then reset it. Also reset client socket."""
        self.socket.close()
        self.socket = None
        self.client_socket = None
    
    # ===== post/pre-sockets ===========================================

    def _wait_and_receive_states_data(self):
        """
        wait until it receives environment data from Godot simulation
        :return: list of dictionaries
        """
        states_data = None
        is_data_received = False
        total_data = bytearray()
        # stay in the loop until data is received
        while is_data_received != True:
            # receive data, specifying what max length it can be in bits.
            data_received = self.client_socket.recv(4096)
            if 4 < len(data_received):
                total_data += data_received
                if len(data_received) < 4096:
                    is_data_received = True
            # checking if the length of the data is enough to be considered valid
    
        states_data = total_data.decode()
        return states_data

    def _get_environment_state(self):
        """
        wait and receive states data and format it to the correct shape
        :return: list of dictionaries
        """
        states_data = self._wait_and_receive_states_data()
        states_data = self._format_states_data(states_data)
        return states_data

    # data formatting ==================================================

    def _create_request(self, initialization=False, termination=False, actions_data=None):
        """
        Handles the type of request to be sent and shape the request into the correct form.
        :param initialization: boolean, indicates if the request must be in the form of an initialization request.
        :param termination: boolean, indicates if the request must be in the form of an termination request.
        :param actions_data: list of dictionaries contaning the fields "name" (string) and "action" (int) the value of
        the action to be taken by the actor.
        :return: The request is a dictionary stored into a string, ready to be sent to the simulator
        """
        request = {}
        request["initialization"] = initialization
        # we send a different seed each time so all episodes are not all
        # the same. but the random seed generator was initialized with the
        # instance's seed
        if initialization:
            request["seed"] = self.random_generator.randint(low=0, high=1e6)
        request["termination"] = termination
        request["render"] = self.is_rendering
        if initialization == False and termination == False:
            request["actions_data"] = self._format_actions_data(actions_data)
        request = json.dumps(request).encode()

        return request

    def _format_actions_data(self, actions_data):
        """
        formats agents data to the correct shape
        :param actions_data: list of dictionaries
        :return: list of dictionaries
        """
        for n_agent in range(len(actions_data)):
            # convert the actions to the correct type
            if isinstance(actions_data[n_agent]["action"], np.integer):
                actions_data[n_agent]["action"] = int(actions_data[n_agent]["action"])
        
        return actions_data

    def _format_states_data(self, state_data):
        """
        returns formatted states data
        :param state_data: list of dictionaries
        :return: list of dictionaries
        """
        state_data = json.loads(state_data)
        for n_agent, agent_data in enumerate(state_data["states_data"]):
            if isinstance(agent_data['state'], str):
                state_data["states_data"][n_agent]["state"] = ast.literal_eval(agent_data["state"])
        return state_data

    def _split_env_data(self, env_data):
        """
        Split the data received by the environment in two lists. One containing rewards and the other containing the
        states
        :param states_data: list of directories
        :return: two lists of directories
        """
        states_data = []
        rewards_data = []
        for env_datum in env_data:
            state_data = {"name": env_datum["name"], "state": env_datum["state"]}
            states_data.append(state_data)
            reward_data = {"name": env_datum["name"], "reward": env_datum["reward"]}
            rewards_data.append(reward_data)
        return states_data, rewards_data



    # simulation functions =============================================



    def _launch_simulation_if_needed(self):
        """If the simulation is not already running, run it with the local godot executable
        """
        if not self.is_godot_launched:
            #self.godot_path_str = get_path(self.godot_path_str, add_absolute=False)
            #self.env_path_str = get_path(self.env_path_str) 
            print(f"environment path: {self.env_path_str}")
            print(f"godot path: {self.godot_path_str}")
            command = "{} --main-pack {}".format(self.godot_path_str, self.env_path_str)
            if not self.is_rendering:
                command = command + " --disable-render-loop --no-window"
            self.godot_process = subprocess.Popen(command, shell=True)
            self.is_godot_launched = True

    def _change_render_type_if_needed(self, render):
        """
        Handling the case where we changed te rendering type and the godot engine is launched (not the first time the
        class is used). We want to close the godot session and create a new one with a different rendering parameter.
        :param render: bool
        :return:
        """
        if (render != self.is_rendering) and self.is_godot_launched:
            if self.socket is None:
                self._initialize_socket()
            self._wait_for_connection()

            self.close()
        self.is_rendering = render

    # other ============================================================

    def scale_states_data(self, states_data):
        """
        Scale states data in a dictionary
        :param states_data: dictionary
        :return: dictionary
        """
        for state_id, state_data in enumerate(states_data):
            state = state_data["state"]
            state = self.scale_state(state)
            states_data[state_id]["state"] = state
        return states_data

    def scale_state(self, state):
        """ Scale a single state (np array)"""
        scaled_state = (state - self.state_min) / (self.state_max - self.state_min)
        return scaled_state